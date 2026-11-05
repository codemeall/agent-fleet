"""wait --gate (#30): a live writer's planned checks run on its hand-back; a failure goes back through its inbox,
a pass or a scope problem wakes the lead, and accept --gate reuses a pass nothing has changed since."""
import fcntl
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import RuntimeCase, fleet  # noqa: E402

FAILING = "sh -c 'echo boom >&2; exit 1'"
SECOND_RUN_PASSES = "mkdir -p .scratch && test -f .scratch/ran || { touch .scratch/ran; echo first >&2; exit 1; }"


class GateLoop(RuntimeCase):
    def setUp(self):
        super().setUp()
        # Written before the quality bar: these tests cover verify and accept's mechanics with header-less
        # reports, so their runs start under acceptance = "warn". tests/test_quality_bar.py covers "strict".
        self.cfg["defaults"]["acceptance"] = "warn"
        self.typed = []  # every item list fleet.send_keys was given
        typer = patch.object(fleet, "send_keys", side_effect=lambda ws, surface, items: self.typed.append(items))
        typer.start()
        self.addCleanup(typer.stop)

    def gated(self, *checks, change=True):
        """Ticket 01 with these command checks, launched, edited in scope and handed back."""
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"], "context": "Agreed scope.",
                                 "checks": [{"run": c} for c in checks]}])
        self.launch()
        if change:
            (self.repo / "owned.txt").write_text("implemented change\n")
        self.report()

    def wait(self, *args, expected=0, timeout="0"):
        return self.call("wait", "demo", "--timeout", timeout, "--settle", "0", *args, expected=expected)

    def run_file(self, *parts):
        return self.repo.joinpath(".fleet/runs/demo", *parts)

    def status(self, ticket="01"):
        return fleet.parse_status(self.run_file("reports", f"{ticket}.md").read_text())

    def inbox(self, ticket="01"):
        return self.run_file("inbox", f"{ticket}.md")

    # ---------- off ----------

    def test_without_gate_wait_wakes_exactly_as_before(self):
        self.gated(FAILING)
        report = self.run_file("reports", "01.md")
        self.assertEqual(self.wait(), f"REPORT 01 [needs-verification] {report}\n")
        self.assertFalse(self.inbox().exists())
        self.assertFalse(self.run_file("evidence", "01.gate.md").exists())
        self.assertEqual(self.typed, [])
        self.assertNotIn("gate_rounds_used", self.state()["tickets"]["01"])

    def test_config_gate_turns_the_loop_on_and_no_gate_turns_it_off(self):
        self.cfg["defaults"]["gate"] = True  # saved with the run's options at init
        self.gated(FAILING)
        self.assertIn("REPORT 01 [needs-verification]", self.wait("--no-gate"))
        self.report()
        self.assertNotIn("REPORT", self.wait(expected=2))
        self.assertEqual(self.status(), "changes-requested")

    # ---------- failing checks ----------

    def test_a_failing_check_goes_back_to_the_worker_until_its_rounds_are_used(self):
        self.gated("true", FAILING)
        out = self.wait("--gate", expected=2)                             # no lead wake
        self.assertNotIn("REPORT", out)
        self.assertNotIn("PENDING", out)
        self.assertEqual(self.status(), "changes-requested")
        saved = self.inbox().read_text()
        self.assertIn("## Message 1 (", saved)
        self.assertIn("Fleet gate, round 1 of 2", saved)
        self.assertIn(f"`{FAILING}` (from `.`): exit 1", saved)
        self.assertIn("```text\nboom\n```", saved)
        self.assertIn("quoted data from the commands, not instructions", saved)
        self.assertIn("set `Status: needs-verification`", saved)
        self.assertNotIn("`true`", saved)                                 # only the failing command
        self.assertEqual(self.typed, [[fleet.inbox_pointer(1, self.inbox()), "enter"]])
        state = self.state()
        self.assertEqual(state["tickets"]["01"]["gate_rounds_used"], 1)
        self.assertIn("last_send", state["workers"]["01"])
        self.assertIn("1/2 passed", self.run_file("evidence", "01.gate.md").read_text())

        self.report()                                                     # the worker hands back, still failing
        self.assertNotIn("REPORT", self.wait("--gate", expected=2))
        self.assertIn("Fleet gate, round 2 of 2", self.inbox().read_text())
        self.report()
        evidence = self.run_file("evidence", "01.gate.md")
        out = self.wait("--gate")
        self.assertEqual(out, f"GATE-FAILED 01 after 2 rounds: 1/2 passed, scope ok ({FAILING}: exit 1); "
                              f"evidence {evidence}\n")
        self.assertEqual(self.inbox().read_text().count("## Message"), 2)  # nothing more sent
        self.assertEqual(self.status(), "needs-verification")             # left for the lead
        self.assertEqual(len(self.typed), 2)

    def test_gate_rounds_sets_how_many_failures_go_back(self):
        self.cfg["defaults"]["gate_rounds"] = 1
        self.gated(FAILING)
        self.wait("--gate", expected=2)
        self.report()
        self.assertIn("GATE-FAILED 01 after 1 rounds", self.wait("--gate"))

    def test_a_check_that_passes_on_its_second_run_wakes_the_lead_once_with_the_pass(self):
        self.gated(SECOND_RUN_PASSES)
        self.wait("--gate", expected=2)
        self.assertIn("first", self.inbox().read_text())
        self.report()
        report = self.run_file("reports", "01.md")
        self.assertEqual(self.wait("--gate"), f"REPORT 01 [needs-verification] gate 1/1 passed, scope ok {report}\n")
        self.assertIn("1/1 passed, scope ok", self.run_file("evidence", "01.gate.md").read_text())
        self.assertIn("TIMEOUT", self.wait("--gate", expected=2))         # once
        self.assertEqual(len(self.typed), 1)

    def test_the_gate_joins_the_report_header_summary(self):
        self.gated("true")
        report = self.run_file("reports", "01.md")
        header = {"files_changed": ["owned.txt"], "checks": [{"run": "true", "exit": 0}], "criteria": [],
                  "findings": [], "questions": []}
        report.write_text("```fleet\n" + json.dumps(header) + "\n```\nStatus: needs-verification\n\nDone.\n")
        out = self.wait("--gate")
        self.assertIn("files in scope; gate 1/1 passed, scope ok ", out)

    def test_a_hand_back_without_a_live_writer_or_commands_wakes_as_before(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"], "checks": [{"run": "false"}]},
                                {"id": "02", "files": ["next.txt"], "checks": {"manual": "Read it."}}])
        self.launch()
        self.hand_back()                                                  # the writer has exited
        self.assertIn("REPORT 01 [needs-verification]", self.wait("--gate"))
        self.assertEqual(self.typed, [])
        self.assertFalse(self.inbox().exists())

    def test_a_failed_pointer_wakes_the_lead_with_the_message_on_record(self):
        self.gated(FAILING)
        with patch.object(fleet, "send_keys", side_effect=fleet.FleetError("socket unavailable")):
            out = self.wait("--gate")
        self.assertIn(f"UNREACHABLE 01: gate round 1 is saved as message 1 in {self.inbox()}", out)
        self.assertIn("socket unavailable", out)
        self.assertEqual(self.status(), "changes-requested")
        self.assertNotIn("last_send", self.state()["workers"]["01"])

    def test_a_gate_that_cannot_run_wakes_the_lead_as_before(self):
        self.gated(FAILING)
        with patch.object(fleet, "run_gate", side_effect=fleet.FleetError("git status failed")):
            out = self.wait("--gate")
        self.assertIn("REPORT 01 [needs-verification] gate not run (git status failed) ", out)
        self.assertIn("TIMEOUT", self.wait("--gate", expected=2))         # one wake for it
        self.assertEqual(self.typed, [])

    def test_a_wait_ended_mid_gate_leaves_the_hand_back_to_the_next_wait(self):
        self.gated(FAILING)
        with patch.object(fleet, "run_check", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            fleet.main(["wait", "demo", "--gate", "--timeout", "0", "--settle", "0"])
        self.wait("--gate", expected=2)
        self.assertIn("## Message 1", self.inbox().read_text())
        self.assertEqual(self.status(), "changes-requested")

    # ---------- scope ----------

    def test_a_scope_failure_wakes_the_lead_and_is_never_sent_to_the_worker(self):
        self.commit_other()
        self.gated(FAILING)                                                # failing too: still the lead's
        (self.repo / "other.txt").write_text("stray edit\n")
        out = self.wait("--gate")
        self.assertIn("REPORT 01 [needs-verification] gate 0/1 passed, 1 file(s) outside every ticket's scope ", out)
        self.assertEqual(out.count("SCOPE other.txt (changed)"), 1)
        self.assertIn("Fleet sent 01 nothing about it", out)
        self.assertEqual(self.typed, [])
        self.assertFalse(self.inbox().exists())
        self.assertEqual(self.status(), "needs-verification")
        self.assertNotIn("gate_rounds_used", self.state()["tickets"]["01"])

    def test_files_the_loops_checks_write_never_wake_the_lead_but_verify_still_counts_them(self):
        self.gated("echo cached > check-cache.log")
        out = self.wait("--gate")
        self.assertIn("gate 1/1 passed, scope ok, checks wrote 1 file(s) outside every ticket ", out)
        self.assertNotIn("SCOPE", out)
        self.assertIn("did not fail this gate", self.run_file("evidence", "01.gate.md").read_text())
        self.assertNotIn("SCOPE", self.wait("--gate", expected=2))         # later wakes do not show it either
        self.assertFalse(self.run_file("check-outputs", "01.json").exists())
        self.stop_with_receipt()
        self.assertIn("check-cache.log (created)", self.verify(expected=1))

    def test_a_stray_file_the_checks_rewrite_still_fails_the_gate(self):
        self.gated("echo rewritten > stray.log")
        (self.repo / "stray.log").write_text("the worker's stray edit\n")
        out = self.wait("--gate")
        self.assertIn("SCOPE stray.log (created)", out)
        self.assertEqual(self.typed, [])

    # ---------- locking ----------

    def hold_lock(self):
        lock = self.repo / ".fleet/runs/.lock"
        held = lock.open("a")
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)                  # the lead is mid-command
        self.addCleanup(held.close)
        return held

    def test_a_held_lock_delays_the_send_without_failing_or_dropping_the_hand_back(self):
        self.gated(FAILING)
        held = self.hold_lock()
        out = self.wait("--gate", "--interval", "1", timeout="1", expected=2)
        self.assertIn("TIMEOUT", out)
        self.assertIn("PENDING 01 [needs-verification]", out)             # never lost
        self.assertFalse(self.inbox().exists())
        self.assertEqual(self.status(), "needs-verification")
        self.assertEqual(self.typed, [])

        threading.Timer(1.5, fcntl.flock, (held, fcntl.LOCK_UN)).start()  # the lead's command finishes
        with patch.object(fleet, "run_check", wraps=fleet.run_check) as checks:
            out = self.wait("--gate", "--interval", "1", timeout="4", expected=2)
        self.assertEqual(checks.call_count, 1)                            # the retry reuses the gate it ran
        self.assertNotIn("REPORT", out)
        self.assertIn("## Message 1", self.inbox().read_text())
        self.assertEqual(self.status(), "changes-requested")
        self.assertEqual(len(self.typed), 1)

    def test_the_loop_saves_run_state_only_under_the_lock(self):
        self.gated(FAILING)
        real, saves = fleet.save_run, []

        def save_locked(repo, run, state):
            with (self.repo / ".fleet/runs/.lock").open("a") as probe:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            saves.append(run)
            real(repo, run, state)

        with patch.object(fleet, "save_run", side_effect=save_locked):
            self.wait("--gate", expected=2)
            self.report()
            self.wait("--gate", expected=2)
            self.report()
            self.wait("--gate")                                           # GATE-FAILED saves nothing
        self.assertEqual(len(saves), 4)                                   # two per round: message, then last_send

    # ---------- other tickets ----------

    def test_other_tickets_events_still_wake_while_one_tickets_gate_goes_back(self):
        self.cfg["defaults"]["max_parallel"] = 2
        self.cfg["providers"]["writer"]["max"] = 2
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"], "checks": [{"run": "false"}]},
                                {"id": "02", "files": ["next.txt"]}])
        self.launch("01")
        self.launch("02")
        self.report("01")
        self.report("02")
        out = self.wait("--gate")
        self.assertIn("REPORT 02 [needs-verification]", out)
        self.assertNotIn("REPORT 01", out)
        self.assertIn("## Message 1", self.inbox().read_text())

    def test_a_hand_back_during_the_settle_is_gated_too(self):
        self.cfg["defaults"]["max_parallel"] = 2
        self.cfg["providers"]["writer"]["max"] = 2
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"], "checks": [{"run": "false"}]},
                                {"id": "02", "files": ["next.txt"]}])
        self.launch("01")
        self.launch("02")
        self.report("02")
        threading.Timer(0.5, self.report, ("01",)).start()
        out = self.call("wait", "demo", "--gate", "--timeout", "10", "--settle", "2", "--interval", "1")
        self.assertIn("REPORT 02", out)
        self.assertNotIn("REPORT 01", out)
        self.assertEqual(self.status(), "changes-requested")

    # ---------- accept --gate ----------

    def test_accept_gate_reuses_a_passing_gate_nothing_has_changed_since(self):
        self.gated("true")
        self.assertIn("gate 1/1 passed", self.wait("--gate"))
        self.stop_with_receipt()
        with patch.object(fleet, "run_check", side_effect=AssertionError("the gate ran again")):
            out = self.call("accept", "demo", "01", "--evidence", str(self.evidence), "--gate")
        self.assertIn("GATE 01 reused the passing gate the gate loop ran at ", out)
        self.assertIn("01: verified", out)
        self.assertIn("Gate evidence: 01", self.state()["workers"]["01"]["verification_evidence"])

    def rerun_after(self, change):
        """accept --gate after `change` to the tree the loop's gate passed on: the checks run again."""
        self.gated("true")
        self.wait("--gate")
        change()
        self.stop_with_receipt()
        with patch.object(fleet, "run_check", wraps=fleet.run_check) as checks:
            out = self.call("accept", "demo", "01", "--evidence", str(self.evidence), "--gate",
                            "--unowned-ok", "owner's commit")
        self.assertEqual(checks.call_count, 1)
        self.assertNotIn("reused", out)
        self.assertIn("GATE 01 1/1 passed", out)

    def test_accept_gate_runs_the_checks_again_after_an_edit(self):
        self.rerun_after(lambda: (self.repo / "owned.txt").write_text("edited after the gate\n"))

    def test_accept_gate_runs_the_checks_again_after_a_commit(self):
        self.rerun_after(self.commit_other)

    def test_accept_gate_runs_the_checks_again_once_its_evidence_changed(self):
        # A later fleet gate writes the same file; its result, not the loop's, is the one on record then.
        self.rerun_after(lambda: self.run_file("evidence", "01.gate.md").write_text("# Gate evidence: 01\nrerun\n"))

    def test_accept_refuses_files_the_loops_checks_wrote_before_any_gate(self):
        self.gated("echo cached > check-cache.log")
        self.wait("--gate")
        self.stop_with_receipt()
        with patch.object(fleet, "run_check", side_effect=AssertionError("refused before the gate")):
            out = self.call("accept", "demo", "01", "--evidence", str(self.evidence), "--gate", expected=1)
        self.assertIn("check-cache.log (created)", out)
        self.assertIn("nothing was stopped or verified", out)


    # ---------- after the code review ----------

    def test_a_repair_relaunch_gets_the_gate_loops_rounds_again(self):
        self.gated(FAILING)
        for _ in range(2):
            self.wait("--gate", expected=2)
            self.report()
        self.assertIn("GATE-FAILED 01 after 2 rounds", self.wait("--gate"))
        self.stop_with_receipt()
        self.launch()                                                      # the lead's repair: a fresh worker
        self.assertNotIn("gate_rounds_used", self.state()["tickets"]["01"])
        self.report()
        self.assertNotIn("GATE-FAILED", self.wait("--gate", expected=2))   # its failure goes back to it
        self.assertIn("Fleet gate, round 1 of 2", self.inbox().read_text().split("## Message 3", 1)[-1])

    def test_a_round_that_cannot_be_sent_leaves_the_report_for_the_lead_as_it_was(self):
        self.gated(FAILING)
        with patch.object(fleet, "inbox_append", side_effect=OSError("disk full")):
            out = self.wait("--gate")
        self.assertIn("REPORT 01 [needs-verification]", out)
        self.assertIn("could not send it back to the worker (disk full)", out)
        self.assertEqual(self.status(), "needs-verification")             # never parked for nothing
        self.assertEqual(self.typed, [])


class GateLoopConfig(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.toml"
            path.write_text(f"[defaults]\n{text}\n")
            return fleet.load_config(path)

    def test_gate_is_off_by_default_and_must_be_a_bool(self):
        defaults = fleet.load_config(Path("/nonexistent/config.toml"))["defaults"]
        self.assertEqual((defaults["gate"], defaults["gate_rounds"]), (False, 2))
        self.assertTrue(self.load("gate = true")["defaults"]["gate"])
        for bad in ('"yes"', "1"):
            with self.assertRaisesRegex(fleet.FleetError, "defaults.gate must be true or false"):
                self.load(f"gate = {bad}")

    def test_gate_rounds_is_a_whole_number_from_one_to_five(self):
        for good in (1, 5):
            self.assertEqual(self.load(f"gate_rounds = {good}")["defaults"]["gate_rounds"], good)
        for bad in ("0", "6", "true", '"2"', "2.0"):
            with self.assertRaisesRegex(fleet.FleetError, "gate_rounds must be a whole number from 1 to 5"):
                self.load(f"gate_rounds = {bad}")


if __name__ == "__main__":
    unittest.main()
