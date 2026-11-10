"""fleet review and fleet accept: the review and acceptance sequence in two calls, refusing what the long form
would refuse, before anything changes where it can."""
import copy
import fcntl
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import RuntimeCase, fleet  # noqa: E402


class ReviewAccept(RuntimeCase):
    def setUp(self):
        super().setUp()
        # Written before the quality bar: these tests cover verify and accept's mechanics with header-less
        # reports, so their runs start under acceptance = "warn". tests/test_quality_bar.py covers "strict".
        self.cfg["defaults"]["acceptance"] = "warn"

    def quits(self):
        """Like stop_with_receipt, for stops made inside review and accept: quitting a worker writes its receipt."""
        def quit_keys(workspace, surface, items):
            for i, w in self.state()["workers"].items():
                if surface in (w.get("surface_id"), w["surface"]):
                    self.receipt(i)
        return patch.object(fleet, "send_keys", side_effect=quit_keys)

    def add_provider(self, name, family, like="reviewer", **fields):
        self.cfg["providers"][name] = dict(copy.deepcopy(self.cfg["providers"][like]), family=family, **fields)
        for tier in self.cfg["providers"][name]["tiers"].values():
            tier.update(model=name + "-model", family=family)

    def evidence_file(self, name, text="Lead checked the diff against every acceptance criterion.\n"):
        path = self.repo / name
        path.write_text(text)
        return str(path)

    def handed_back(self, ticket="01", change="implemented change\n"):
        self.launch(ticket)
        (self.repo / "owned.txt").write_text(change)
        self.report(ticket)

    def review(self, *args, expected=0, ticket="01"):
        with self.quits():
            return self.call("review", "demo", ticket, *args, expected=expected)

    def accept(self, *args, expected=0, ticket="01"):
        with self.quits():
            return self.call("accept", "demo", ticket, "--evidence", str(self.evidence), *args, expected=expected)

    # ---------- review ----------

    def test_review_stops_the_writer_saves_the_diff_writes_the_prompt_and_launches_another_family(self):
        self.setup_run(review="cross-all")
        self.handed_back()
        out = self.review()
        self.assertIn("01: exited (surface:1)", out)
        self.assertIn("DIFF .fleet/runs/demo/evidence/01.diff (1 file)", out)
        self.assertIn("REVIEW review-01 reviewer:reviewer-model@high family=anthropic on surface:2", out)
        state = self.state()
        self.assertEqual(state["workers"]["01"]["state"], "exited")
        reviewer = state["workers"]["review-01"]
        self.assertEqual((reviewer["role"], reviewer["review_of"], reviewer["tier"], reviewer["state"]),
                         ("review", "01", "review", "running"))
        diff = (self.repo / ".fleet/runs/demo/evidence/01.diff").read_text()
        self.assertIn("+implemented change", diff)
        self.assertEqual(reviewer["diff_sha"], fleet.digest(diff.encode()))
        prompt = (self.repo / ".fleet/runs/demo/prompts/review-01.md").read_text()
        self.assertIn("01", prompt)

    def test_review_prints_the_reviewers_first_screen_after_the_lock_is_released(self):
        self.setup_run(review="cross-all")
        self.handed_back()
        seen = []

        def screen(term, w, settle, run=None):
            with (self.repo / ".fleet/runs/.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises while the review still holds it
                fcntl.flock(lock, fcntl.LOCK_UN)
            seen.append((w["id"], settle, run))
        with patch.object(fleet, "print_start_screen", side_effect=screen):
            self.review("--settle", "2")
        self.assertEqual(seen, [("review-01", 2.0, "demo")])

    def test_review_takes_the_first_fitting_provider_in_prefer_then_pool_order(self):
        self.add_provider("second", "google")
        self.cfg["defaults"]["prefer"] = ["second", "reviewer"]
        self.setup_run(review="cross-all")
        self.handed_back()
        self.assertIn("REVIEW review-01 second:second-model@high family=google", self.review())

    def test_review_follows_the_routing_pool_order_and_a_pinned_model(self):
        self.add_provider("second", "google", models_allow=["second-model", "pinned-model"])
        self.cfg["providers"]["second"]["model_families"] = {"pinned-model": "google"}
        self.cfg["defaults"]["prefer"] = ["reviewer", "second"]
        self.cfg["defaults"]["routing"] = "agents=writer,second:pinned-model,reviewer"
        self.setup_run(review="cross-all")
        self.handed_back()
        self.assertIn("REVIEW review-01 second:pinned-model@high family=google", self.review())

    def test_review_refuses_with_each_candidates_reason_when_only_the_writers_family_is_left(self):
        self.setup_run(review="cross-all")  # plan refuses an impossible review, so the pool shrinks after it
        self.cfg["providers"]["reviewer"]["family"] = "openai"
        for tier in self.cfg["providers"]["reviewer"]["tiers"].values():
            tier["family"] = "openai"
        self.add_provider("off", "google", enabled=False)
        self.cfg["defaults"]["prefer"] = ["off"]
        self.handed_back()
        out = self.review(expected=1)
        self.assertIn("no provider can review 01 now; its writer ran writer:writer-model (family openai)", out)
        self.assertIn("  off: provider 'off' is turned off in config", out)
        self.assertIn("  writer: same family as the writer (openai)", out)
        self.assertIn("  reviewer: same family as the writer (openai)", out)
        self.assertIn("tell the owner", out)
        # Refused before anything changed: the writer still runs and there is no diff or reviewer.
        self.assertEqual(self.state()["workers"]["01"]["state"], "running")
        self.assertFalse((self.repo / ".fleet/runs/demo/evidence/01.diff").exists())
        self.assertNotIn("review-01", self.state()["prompts"])

    def test_review_names_capacity_and_checks_an_explicit_provider_before_stopping(self):
        self.cfg["defaults"]["max_parallel"] = 2
        self.cfg["providers"]["writer"]["max"] = 2
        self.setup_run(review="cross-all", tickets=[{"id": "01", "files": ["owned.txt"]},
                                                   {"id": "02", "files": ["next.txt"]}])
        self.launch("01")
        self.launch("02")
        (self.repo / "owned.txt").write_text("implemented change\n")
        (self.repo / "next.txt").write_text("second change\n")
        self.report("01")
        self.report("02")
        self.review()
        out = self.review(expected=1, ticket="02")
        self.assertIn("  reviewer: at capacity (reviewer.max 1)", out)
        self.assertIn("If capacity is the only reason, stop a finished worker", out)
        out = self.review("--provider", "reviewer", expected=1, ticket="02")
        self.assertIn("reviewer cannot review 02: at capacity (reviewer.max 1)", out)
        self.assertEqual(self.state()["workers"]["02"]["state"], "running")
        self.assertIn("same family as the writer", self.review("--provider", "writer", expected=1, ticket="02"))

    def test_review_pins_need_a_provider(self):
        self.setup_run(review="cross-all")
        self.handed_back()
        for flag in ("--model", "--effort", "--family"):
            self.assertIn("pass --provider with them", self.review(flag, "x", expected=1))
        out = self.review("--provider", "reviewer", "--effort", "low")
        self.assertIn("REVIEW review-01 reviewer:reviewer-model@low family=anthropic", out)

    def test_a_family_flag_never_contradicts_the_config(self):
        """Final review, 5: --family names a family only where the config knows none, so the writer's own provider
        cannot review as another family; a plan's family pin is held to the same rule."""
        self.setup_run(review="cross-all")
        self.handed_back()
        out = self.review("--provider", "writer", "--family", "anthropic", expected=1)
        self.assertIn("writer cannot review 01: family anthropic contradicts the config, which puts writer-model in "
                      "family openai", out)
        self.assertEqual(self.state()["workers"]["01"]["state"], "running")  # refused before the writer stopped
        self.assertNotIn("review-01", self.state()["workers"])
        # A model the config places in no family takes the flag's.
        self.cfg["providers"]["cursor"] = dict(copy.deepcopy(self.cfg["providers"]["reviewer"]), family="mixed")
        for tier in self.cfg["providers"]["cursor"]["tiers"].values():
            tier.update(model="auto")
            del tier["family"]
        out = self.review("--provider", "cursor", "--family", "google")
        self.assertIn("REVIEW review-01 cursor:auto@high family=google", out)

    def test_a_plans_family_pin_never_contradicts_the_config(self):
        self.call("init", "demo", "--workspace", "workspace:1", "--review", "cross-all")
        (self.repo / "01.md").write_text("# Ticket 01\nImplement owned change and verify its behavior.\n")
        plan = self.repo / "plan.json"
        plan.write_text(json.dumps({"tickets": [{"id": "01", "ticket": str(self.repo / "01.md"),
                                                       "files": ["owned.txt"], "provider": "writer",
                                                       "tier": "standard", "family": "anthropic"}]}))
        out = self.call("plan", "demo", "--file", str(plan), expected=1)
        self.assertIn("01: family anthropic contradicts the config, which puts writer-model in family openai", out)
        self.assertEqual(self.state()["tickets"], {})

    def test_review_refuses_an_investigate_ticket_and_one_not_launched(self):
        self.setup_run(review="cross-all", tickets=[{"id": "look", "kind": "investigate", "files": []},
                                                   {"id": "01", "files": ["owned.txt"]}])
        self.assertIn("has not launched yet", self.review(expected=1))
        self.launch("look")
        self.report("look")
        self.assertIn("investigate ticket: it has no diff", self.review(expected=1, ticket="look"))
        self.assertEqual(self.state()["workers"]["look"]["state"], "running")

    def test_a_long_ticket_id_needs_an_explicit_reviewer_id(self):
        long_id = "t" * 60
        self.setup_run(review="cross-all", tickets=[{"id": long_id, "files": ["owned.txt"]}])
        self.launch(long_id)
        self.report(long_id)
        self.assertIn("name the reviewer with --as", self.review(expected=1, ticket=long_id))
        self.assertIn("ticket's ID", self.review("--as", long_id, expected=1, ticket=long_id))
        self.assertIn("REVIEW rev-long reviewer:", self.review("--as", "rev-long", ticket=long_id))

    def test_a_new_round_replaces_the_last_but_never_a_live_reviewer(self):
        self.cfg["defaults"]["review_rounds"] = 3  # three rounds below; the cap is tested in test_review_rounds.py
        self.setup_run(review="cross-all")
        self.handed_back()
        self.review()
        self.assertIn("reviewer review-01 may still be running", self.review(expected=1))
        self.report("review-01")
        self.stop_with_receipt("review-01")
        # The writer fixes a finding; the next round's diff covers both rounds from the first launch.
        self.launch("01")
        (self.repo / "owned.txt").write_text("fixed change\n")
        self.report("01")
        out = self.review()
        self.assertIn("01: exited", out)
        self.assertIn("REVIEW review-01", out)
        prompt_diff = (self.repo / ".fleet/runs/demo/snapshots/review-01.diff").read_text()
        self.assertIn("+fixed change", prompt_diff)
        self.assertEqual(len(list((self.repo / ".fleet/runs/demo/reports").glob("review-01.*.previous"))), 1)
        self.report("review-01")
        self.stop_with_receipt("review-01")
        self.assertIn("already stopped", self.review())

    def test_review_respects_the_scope_check_and_passes_unowned_ok_through(self):
        self.setup_run(review="cross-all")
        self.handed_back()
        (self.repo / "stray.txt").write_text("owner's note\n")
        out = self.review(expected=1)
        self.assertIn("stray.txt (created)", out)
        self.assertIn("Nothing was stopped", out)                           # refused before the writer stops
        self.assertEqual(self.state()["workers"]["01"]["state"], "running")
        self.assertNotIn("review-01", self.state()["workers"])
        out = self.review("--unowned-ok", "owner's scratch note")
        self.assertIn("01: exited", out)
        self.assertIn("REVIEW review-01", out)
        self.assertTrue(any("stray.txt" in d and "owner's scratch note" in d for d in self.state()["decisions"]))

    def test_a_failed_writer_stop_refuses_before_any_diff(self):
        self.setup_run(review="cross-all")
        self.handed_back()
        with patch.object(fleet, "send_keys"), patch.object(fleet, "STOP_WAIT_S", 0):
            out = self.call("review", "demo", "01", expected=1)
        self.assertIn("stop failed, so no review of 01 started", out)
        self.assertEqual(self.state()["workers"]["01"]["state"], "stop-failed")
        self.assertFalse((self.repo / ".fleet/runs/demo/evidence/01.diff").exists())

    # ---------- accept ----------

    def reviewed(self):
        self.setup_run(review="cross-all")
        self.handed_back()
        self.review()
        self.report("review-01")

    def test_accept_verifies_the_reviewer_then_the_writer_and_lists_what_it_unblocked(self):
        self.reviewed()
        out = self.accept()
        self.assertIn("review-01: exited", out)  # accept stopped the reviewer itself
        self.assertLess(out.index("review-01: verified"), out.index("01: verified"))
        self.assertIn("UNBLOCKED 02", out)
        state = self.state()
        self.assertTrue(state["workers"]["review-01"]["verified"])
        self.assertEqual(state["tickets"]["01"]["status"], "verified")
        self.assertNotIn("02", state["workers"])

    def test_accept_launch_ready_launches_the_unblocked_tickets(self):
        self.reviewed()
        out = self.accept("--launch-ready")
        self.assertIn("UNBLOCKED 02", out)
        self.assertIn("LAUNCHED 02 writer:writer-model@high standard surface:3", out)
        self.assertEqual(self.state()["workers"]["02"]["state"], "running")

    def test_accept_uses_its_own_review_evidence_when_given(self):
        self.reviewed()
        review_notes = self.evidence_file("review-notes.md", "Reviewer found no issues in the 01 diff.\n")
        self.accept("--review-evidence", review_notes)
        state = self.state()
        self.assertIn("found no issues", state["workers"]["review-01"]["verification_evidence"])
        self.assertNotIn("found no issues", state["workers"]["01"]["verification_evidence"])

    def test_accept_needs_no_reviewer_when_review_is_not_required_or_for_an_investigation(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]},
                                {"id": "02", "files": ["next.txt"], "blockers": ["01"]},
                                {"id": "03", "files": ["third.txt"], "blockers": ["01", "02"]}])
        self.handed_back()
        out = self.accept()  # the writer is still running: accept stops it first
        self.assertIn("01: exited", out)
        self.assertIn("01: verified", out)
        self.assertIn("UNBLOCKED 02\n", out)  # 03 still waits on 02

    def test_accept_takes_an_investigate_ticket_under_cross_all(self):
        self.setup_run(review="cross-all", tickets=[{"id": "look", "kind": "investigate", "files": []},
                                                   {"id": "01", "files": ["owned.txt"], "blockers": ["look"]}])
        self.launch("look")
        self.report("look")
        out = self.accept(ticket="look")
        self.assertIn("look: verified", out)
        self.assertIn("UNBLOCKED 01", out)

    def test_accept_without_a_required_review_refuses_every_problem_before_stopping_anything(self):
        self.setup_run(review="cross-all")
        self.launch()
        (self.repo / "owned.txt").write_text("implemented change\n")
        out = self.call("accept", "demo", "01", "--evidence", str(self.repo / "missing.md"),
                        "--review-evidence", str(self.evidence), "--gate", expected=1)
        self.assertIn("01 not accepted; nothing was stopped or verified", out)
        self.assertIn("- 01 needs a cross-family review first: fleet review demo 01", out)
        self.assertIn("- 01's report says nothing (no report yet)", out)
        self.assertIn("- --review-evidence: no reviewer of 01 awaits verification", out)
        self.assertIn("- --evidence: ", out)
        self.assertIn("- 01 has no runnable checks to gate", out)
        self.assertEqual(self.state()["workers"]["01"]["state"], "running")

    def test_accept_refuses_a_stale_review(self):
        self.reviewed()
        self.stop_with_receipt("review-01")
        (self.repo / "owned.txt").write_text("changed after review\n")
        out = self.accept(expected=1)
        self.assertIn("review-01 reviewed an earlier diff of 01; run fleet review demo 01 again", out)
        self.assertFalse(self.state()["workers"]["review-01"].get("verified"))

    def reviewed_with_checks(self, run):
        self.setup_run(review="cross-all", tickets=[{"id": "01", "files": ["owned.txt"], "checks": [{"run": run}]}])
        self.handed_back()
        self.review()
        self.report("review-01")

    def test_accept_gate_failure_verifies_nothing(self):
        self.reviewed_with_checks("exit 3")
        out = self.accept("--gate", expected=1)
        self.assertIn("FAIL exit 3: exit 3", out)
        self.assertIn("gate failed (above), so nothing was verified", out)
        state = self.state()
        self.assertEqual(state["tickets"]["01"]["status"], "pending")
        self.assertFalse(state["workers"]["review-01"].get("verified"))

    def test_accept_gate_pass_adds_its_evidence_for_the_writer(self):
        self.reviewed_with_checks("true")
        self.assertIn("GATE 01 1/1 passed, scope ok", self.accept("--gate"))
        state = self.state()
        self.assertEqual(state["tickets"]["01"]["status"], "verified")
        self.assertIn("Gate evidence: 01", state["workers"]["01"]["verification_evidence"])
        self.assertNotIn("Gate evidence", state["workers"]["review-01"]["verification_evidence"])

    def test_evidence_written_after_launch_is_the_leads_not_a_scope_change(self):
        self.reviewed_with_checks("true")
        notes = self.evidence_file("notes.md")
        with self.quits():
            out = self.call("accept", "demo", "01", "--evidence", notes, "--gate")
        self.assertIn("GATE 01 1/1 passed, scope ok", out)
        self.assertIn("notes.md", self.state()["lead_files"])

    def test_accept_respects_the_scope_check_and_passes_unowned_ok_through(self):
        self.setup_run()
        self.handed_back()
        (self.repo / "stray.txt").write_text("owner's note\n")
        out = self.accept(expected=1)
        self.assertIn("stray.txt (created)", out)
        self.assertIn("nothing was stopped or verified", out)
        self.assertEqual(self.state()["workers"]["01"]["state"], "running")
        out = self.accept("--unowned-ok", "owner's scratch note", "--gate", expected=1)  # no runnable checks
        self.assertIn("no runnable checks to gate", out)
        out = self.accept("--unowned-ok", "owner's scratch note")
        self.assertIn("01: verified", out)
        self.assertTrue(any("stray.txt" in d and "owner's scratch note" in d for d in self.state()["decisions"]))

    def test_unowned_ok_lets_the_gates_own_scope_check_pass(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"], "checks": [{"run": "true"}]}])
        self.handed_back()
        (self.repo / "stray.txt").write_text("owner's note\n")
        out = self.accept("--gate", "--unowned-ok", "owner's scratch note")
        self.assertIn("GATE 01 1/1 passed, scope ok", out)
        self.assertIn("01: verified", out)

    def test_a_writer_refused_after_its_reviewer_says_what_was_recorded_and_a_rerun_finishes(self):
        self.reviewed()
        real = fleet.cmd_verify

        def verify(a, cfg):
            if a.id == "01":
                raise fleet.FleetError("simulated refusal")
            return real(a, cfg)
        with patch.object(fleet, "cmd_verify", side_effect=verify):
            out = self.accept(expected=1)
        self.assertIn("review-01 is verified and recorded, but 01 is not: simulated refusal", out)
        self.assertIn("run fleet accept demo 01 again", out)
        state = self.state()
        self.assertTrue(state["workers"]["review-01"]["verified"])
        self.assertEqual(state["tickets"]["01"]["status"], "pending")
        out = self.accept()
        self.assertNotIn("review-01: verified", out)
        self.assertIn("01: verified", out)
        self.assertIn("UNBLOCKED 02", out)

    def test_accept_points_a_reviewer_id_at_its_ticket_and_refuses_finished_tickets(self):
        self.reviewed()
        self.assertIn("name the ticket it reviews: fleet accept demo 01", self.accept(expected=1, ticket="review-01"))
        self.accept()
        self.assertIn("already verified", self.accept(expected=1))
        self.assertIn("already verified", self.review(expected=1))

    def test_review_refuses_a_writer_that_has_not_handed_back_without_stopping_it(self):
        self.setup_run(review="cross-all")
        self.launch()
        out = self.review(expected=1)
        self.assertIn("review it once it hands back", out)
        self.assertEqual(self.state()["workers"]["01"]["state"], "running")

    def test_a_new_review_round_gets_its_own_scope_window_and_a_fresh_inbox(self):
        self.setup_run(review="cross-all")
        self.handed_back()
        self.review()
        runs = self.repo / ".fleet/runs/demo"
        first_tree = (runs / "snapshots/review-01.tree.json").read_text()
        self.call("send", "demo", "review-01", "round one note")
        self.report("review-01")
        self.stop_with_receipt("review-01")
        self.launch("01")                                                    # a fix round for the writer
        (self.repo / "owned.txt").write_text("round two fix\n")
        self.report("01")
        self.review()
        self.assertNotEqual((runs / "snapshots/review-01.tree.json").read_text(), first_tree)
        self.assertFalse((runs / "inbox/review-01.md").exists())
        self.assertTrue(list((runs / "inbox").glob("review-01.*.previous")))


if __name__ == "__main__":
    import unittest
    unittest.main()
